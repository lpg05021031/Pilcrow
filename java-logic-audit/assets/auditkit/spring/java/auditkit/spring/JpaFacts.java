package auditkit.spring;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.springframework.context.ApplicationContext;

/**
 * How each JPA entity is really mapped: its table, and the columns behind each property, as the
 * persistence provider resolved them (naming strategy included). Everything is done by reflection,
 * so this compiles whether the project uses javax.persistence, jakarta.persistence or no JPA at all.
 */
public final class JpaFacts {

    private JpaFacts() {
    }

    public static Map<String, Object> collect(ApplicationContext context) {
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        Map<String, Object> entities = new TreeMap<String, Object>();
        out.put("entities", entities);
        Class<?> emfType = firstPresent("jakarta.persistence.EntityManagerFactory", "javax.persistence.EntityManagerFactory");
        if (emfType == null || context.getBeanNamesForType(emfType).length == 0) {
            out.put("provider", null);
            return out;
        }
        Object emf = context.getBean(emfType);
        try {
            Class<?> sfType = Class.forName("org.hibernate.engine.spi.SessionFactoryImplementor");
            Object sessionFactory = emfType.getMethod("unwrap", Class.class).invoke(emf, sfType);
            out.put("provider", "hibernate");
            Object metamodel = emfType.getMethod("getMetamodel").invoke(emf);
            for (Object entityType : (Iterable<?>) call(metamodel, "getEntities")) {
                Class<?> javaType = (Class<?>) call(entityType, "getJavaType");
                Object persister = persister(sessionFactory, javaType.getName());
                if (persister != null) {
                    entities.put(javaType.getName(), describe(persister));
                }
            }
        } catch (Exception e) {
            out.put("provider", "unknown");
            out.put("error", e.getClass().getName() + ": " + e.getMessage());
        }
        return out;
    }

    private static Object persister(Object sessionFactory, String entityName) throws Exception {
        try {                                                   // Hibernate 6
            Object mapping = call(sessionFactory, "getMappingMetamodel");
            return mapping.getClass().getMethod("getEntityDescriptor", String.class).invoke(mapping, entityName);
        } catch (NoSuchMethodException e) {                     // Hibernate 5
            Object metamodel = call(sessionFactory, "getMetamodel");
            return metamodel.getClass().getMethod("entityPersister", String.class).invoke(metamodel, entityName);
        }
    }

    private static Map<String, Object> describe(Object persister) throws Exception {
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("table", String.valueOf(call(persister, "getTableName")).toLowerCase());
        Map<String, Object> id = new LinkedHashMap<String, Object>();
        id.put("property", call(persister, "getIdentifierPropertyName"));
        id.put("columns", lower((String[]) call(persister, "getIdentifierColumnNames")));
        out.put("id", id);
        String[] names = (String[]) call(persister, "getPropertyNames");
        Object[] types = (Object[]) call(persister, "getPropertyTypes");
        boolean versioned = Boolean.TRUE.equals(call(persister, "isVersioned"));
        int versionIndex = versioned ? ((Number) call(persister, "getVersionProperty")).intValue() : -1;
        out.put("version", versionIndex >= 0 ? names[versionIndex] : null);
        Map<String, Object> properties = new TreeMap<String, Object>();
        Method columnsOf = find(persister.getClass(), "getPropertyColumnNames", String.class);
        for (int i = 0; i < names.length; i++) {
            Map<String, Object> p = new LinkedHashMap<String, Object>();
            Object type = types[i];
            p.put("type", String.valueOf(call(type, "getName")));
            boolean collection = Boolean.TRUE.equals(call(type, "isCollectionType"));
            p.put("association", Boolean.TRUE.equals(call(type, "isAssociationType")));
            p.put("collection", collection);
            p.put("columns", collection ? new ArrayList<String>() : lower((String[]) columnsOf.invoke(persister, names[i])));
            properties.put(names[i], p);
        }
        out.put("properties", properties);
        return out;
    }

    private static Object call(Object target, String method) throws Exception {
        Method m = find(target.getClass(), method);
        return m.invoke(target);
    }

    private static Method find(Class<?> type, String name, Class<?>... params) throws NoSuchMethodException {
        Method m = type.getMethod(name, params);
        m.setAccessible(true);
        return m;
    }

    private static List<String> lower(String[] values) {
        List<String> out = new ArrayList<String>();
        if (values != null) {
            for (String v : Arrays.asList(values)) {
                out.add(v == null ? null : v.toLowerCase());
            }
        }
        return out;
    }

    private static Class<?> firstPresent(String... names) {
        for (String name : names) {
            try {
                return Class.forName(name);
            } catch (ClassNotFoundException e) {
                // try the next
            }
        }
        return null;
    }
}
